
context: You are a experienced solution architect designing enterprise grade systems . Implement entire AWS architecture 

- use aws sandbox1 profile , its licensed , you are free to use the necessary services 

-ask further in-depth questions on architecture to clear your goal and make the most accurate architecture possible

  current scenario:

- you have an excel file containing two coloumns with extremely large queries  tquerying to ORACLE and SNOWFLAKE databases 
- a python script reads these queries and run them on the databases and then matches the result.
- each query is about 50 characters big, extremely large queries 
- we must see that the values returned must be consistent between the two databases


   task 

- entire workflow should be shifted to AWS CLOUD 
- base image of python willl be pulled, dependencies will installed in the build phase and be pushed into HARBOR or any similar service 
- step functions will be involved as query runtime is high 
- it should be automatided and jobs will be run everyday 
- design a indepth cloud architecture that optimises operational overhead
- containers run image as there are custom built images which have the necessary dependencies 
- operational overhead should be minimised 
- jobs will be run incosistently so on demand pricing is recommended 


   GOAL:

- make enterprise grade workflow , use best optimal services 
- company is open to monolithic architecture entirely on AWS 
- latency and querying speed should be prioritised , the system should be well optmised and fast while operational overhead should be minimal 

- emulate a RDBMS connection and have a sample excel sheet a table with two columns and around 20 big sample queries with the above workflow and each query belongs to two different relational database systems

- create big sample fake data using amazon RDS , of two different database systems , main goal is to verify with this workflow that queries run on both of these systems will give consistent outputs 

Excel sheet in s3, python reads,  , creates data frame , runs step functions and verifies if queries are consistent between two systems 

All this occurs in dockerised container with base image python , necessary dependencies are created with aws code build , image created is pushed into ECR 

Store all necessary files like initial Dockerfile in S3

The app is deployed on ECS margate 

-make sure everything is server less and on demand 

- create in-depth terraform architecture file 

- prioritise low operational overhead 

-give extra focus in step function creation , entire workflow depends a lot on it 

- the entire AWS architecture must be reproducible on different AWS consoles as well , so priorities that too 



